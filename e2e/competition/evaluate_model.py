#!/usr/bin/env python3
"""The competition demo's evaluation job: score the model that was trained.

    python evaluate_model.py --artifacts /home/user/.flashml/artifacts.json \
                             --model-key model.json --out /work/out

This is the workload the hibernating FC Sandbox worker wakes up to run. It
never opens a hardcoded path to a model. It reads the artifact document the
orchestrator writes on the wake path
(`sandbox_orchestrator.artifacts_document` /
`evaluation_artifacts_path`, schema `flashml.sandbox-artifacts/v1`), selects
its model from that document's `objects` map by key, and fetches it over
plain HTTPS.

**No credential is involved anywhere in this file, and that is deliberate.**
Every URL in `artifacts.json` is presigned: the signature is in the query
string and it expires on its own. There is no access key to look for, no
environment variable to read, no SDK to configure — if this file ever needed
one, the wake path would be wrong. It follows that the document itself is a
bearer credential, which is why the orchestrator writes it at 0600 with
`write_file` and never through a command line.

**The reported accuracy is tied to a specific model by sha256.** The trainer
records the hash of the exact `model.json` bytes it wrote; this job hashes the
exact bytes it downloaded and reports them; `--expect-sha256` turns that from
evidence into an assertion. A model that does not match, does not parse, or
does not carry the architecture it claims is **refused, not scored** — a
wrong number that looks right is worse than a failed task, and a failed task
is a thing the coordinator already knows how to retry.

The held-out set is rebuilt from the `data_recipe` embedded in the model, so
the evaluation needs no dataset input, no network beyond the one fetch, and
no agreement with the trainer beyond the model file itself.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from workload_common import (
    EVAL_METRICS_SCHEMA,
    MODEL_SCHEMA,
    WorkloadError,
    atomic_write_json,
    evaluate_params,
    load_json_document,
    param_shapes,
    parse_json_document,
    sha256_hex,
    synth_dataset,
    work_dir,
)

#: The schema `sandbox_orchestrator.ARTIFACT_MANIFEST_SCHEMA` stamps. Checked
#: rather than assumed, for the reason that module states about its own
#: manifests: a consumer reading a document it does not understand must be
#: able to say so rather than guess.
ARTIFACTS_SCHEMA = "flashml.sandbox-artifacts/v1"

#: Where the orchestrator puts it — `SandboxPaths.control_dir` +
#: `ARTIFACTS_NAME`. A default, not a constant the code depends on: the path
#: is a flag so a different deployment (or this repo's own local rehearsal)
#: can point at its own document.
DEFAULT_ARTIFACTS_PATH = "/home/user/.flashml/artifacts.json"

#: A ceiling on what one fetch may pull into a sandbox with a finite disk.
#: The demo's model is a few hundred kilobytes; anything near this bound means
#: the key selected is not the one intended.
MAX_MODEL_BYTES = 64 * 1024 * 1024

FETCH_TIMEOUT_S = 60.0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    work = work_dir()
    parser = argparse.ArgumentParser(
        prog="evaluate_model.py", description=__doc__.splitlines()[0]
    )
    parser.add_argument("--artifacts", default=DEFAULT_ARTIFACTS_PATH,
                        help="the orchestrator's artifacts.json (presigned URLs)")
    parser.add_argument("--model-key", default="model.json",
                        help="which object in artifacts.json to evaluate, matched "
                             "against the tail of each OSS key")
    parser.add_argument("--out", default=f"{work}/out",
                        help="output directory (metrics.json)")
    parser.add_argument("--expect-sha256", default="",
                        help="refuse to score a model whose bytes do not hash to "
                             "this — the training job's reported model_sha256")
    parser.add_argument("--save-model", default="",
                        help="optional path to keep the fetched model at, for a "
                             "human to inspect after the demo")
    return parser.parse_args(argv)


# ---------------------------------------------------------------------------
# The artifact document
# ---------------------------------------------------------------------------


def read_artifacts(path: Path) -> dict[str, Any]:
    document = load_json_document(path, f"artifacts document {path}")
    schema = document.get("schema")
    if schema != ARTIFACTS_SCHEMA:
        raise WorkloadError(
            f"artifacts document declares schema {schema!r}; this workload reads "
            f"{ARTIFACTS_SCHEMA!r}. Refusing to guess at a document shape it does "
            f"not understand."
        )
    objects = document.get("objects")
    if not isinstance(objects, dict) or not objects:
        raise WorkloadError(
            "artifacts document carries no 'objects' — the orchestrator refuses "
            "to queue this work before the presign lands, so an empty map here "
            "means the file was written by something else"
        )
    return document


def select_object(objects: dict[str, Any], wanted: str) -> tuple[str, str]:
    """`(key, url)` for the one object this job is about.

    Matched on the tail of the key rather than the whole of it: keys are
    `jobs/<job_id>/<task_id>/model.json`, and neither the job id nor the task
    id is knowable when the job spec is written. Ambiguity is refused rather
    than resolved by sort order — two tasks that both produced a `model.json`
    is a real possibility for a swept job, and silently scoring whichever one
    sorted first would attribute one task's accuracy to another's model.
    """
    matches = sorted(
        key for key in objects
        if key == wanted or key.endswith("/" + wanted.lstrip("/"))
    )
    if not matches:
        available = ", ".join(sorted(objects)[:12])
        raise WorkloadError(
            f"no object in artifacts.json matches {wanted!r}; available keys: "
            f"{available}"
        )
    if len(matches) > 1:
        raise WorkloadError(
            f"{len(matches)} objects match {wanted!r} ({', '.join(matches)}) — "
            f"refusing to guess which model the evaluation is about"
        )
    key = matches[0]
    url = objects[key]
    if not isinstance(url, str) or not url:
        raise WorkloadError(f"object {key!r} has no URL in artifacts.json")
    return key, url


def check_url(url: str) -> None:
    """HTTPS, or loopback HTTP for a local rehearsal.

    The presigned URL is the only network this workload is allowed, and its
    signature is the only thing standing between the fetch and a substituted
    model, so plain HTTP to a remote host is refused outright rather than
    warned about. Loopback is admitted for exactly one reason: `run_local_
    recovery.sh` and `test_workloads.py` serve the model from 127.0.0.1 so the
    real fetch path is what gets exercised, instead of a `file://` shortcut
    that would leave the HTTP code untested until the night of the demo.
    """
    parts = urlsplit(url)
    if parts.scheme == "https":
        return
    if parts.scheme == "http" and parts.hostname in ("127.0.0.1", "localhost", "::1"):
        return
    raise WorkloadError(
        f"refusing to fetch a model over {parts.scheme or 'an unknown scheme'}://"
        f"{parts.hostname or '?'} — a presigned artifact URL is https, and the "
        f"signature is the only integrity this fetch has"
    )


def fetch(url: str) -> bytes:
    check_url(url)
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT_S) as response:
            declared = response.headers.get("Content-Length")
            if declared is not None and int(declared) > MAX_MODEL_BYTES:
                raise WorkloadError(
                    f"model object declares {declared} bytes, above this "
                    f"workload's {MAX_MODEL_BYTES} ceiling"
                )
            # Read one byte past the ceiling so an object with no
            # Content-Length is bounded too — a chunked response could
            # otherwise fill the sandbox's disk.
            data = response.read(MAX_MODEL_BYTES + 1)
    except urllib.error.HTTPError as exc:
        # The URL is never echoed: it carries its signature in the query
        # string, and this message goes to stderr, into the task log, and from
        # there into an artifact anyone with the job can read.
        raise WorkloadError(
            f"fetching the model failed with HTTP {exc.code} {exc.reason} — a "
            f"presigned URL that has expired looks exactly like this"
        ) from None
    except (urllib.error.URLError, OSError) as exc:
        raise WorkloadError(f"fetching the model failed: {exc.__class__.__name__}") from None
    if len(data) > MAX_MODEL_BYTES:
        raise WorkloadError(
            f"model object exceeds this workload's {MAX_MODEL_BYTES} byte ceiling"
        )
    if not data:
        raise WorkloadError("the model object is empty")
    return data


# ---------------------------------------------------------------------------
# The model
# ---------------------------------------------------------------------------


def load_model(raw: bytes, source: str) -> dict[str, Any]:
    """A model this workload can score, or an exception.

    Every check here exists so that a bad model fails the *task* rather than
    producing a number. Structural validation is not paranoia at this layer:
    the bytes arrived over the network, and a truncated download is a
    perfectly ordinary event that would otherwise surface as an IndexError
    deep inside a matrix multiply.
    """
    document = parse_json_document(raw, source)
    if document.get("schema") != MODEL_SCHEMA:
        raise WorkloadError(
            f"{source}: schema is {document.get('schema')!r}, expected "
            f"{MODEL_SCHEMA!r} — this is not a model this evaluator can score"
        )
    params = document.get("params")
    architecture = document.get("architecture")
    recipe = document.get("data_recipe")
    if not isinstance(params, dict) or not isinstance(architecture, dict) \
            or not isinstance(recipe, dict):
        raise WorkloadError(f"{source}: missing 'params', 'architecture' or 'data_recipe'")
    for name in ("W1", "b1", "W2", "b2"):
        if name not in params:
            raise WorkloadError(f"{source}: model is missing parameter {name!r}")
    try:
        shapes = param_shapes(params)
    except (TypeError, IndexError, KeyError):
        raise WorkloadError(f"{source}: model parameters are not rectangular arrays") from None
    declared = architecture.get("shapes")
    if declared is not None and declared != shapes:
        raise WorkloadError(
            f"{source}: parameter shapes {shapes} contradict the architecture the "
            f"model declares ({declared}) — refusing to score a model whose own "
            f"description does not match it"
        )
    if shapes["W1"][1] != int(recipe.get("features", -1)):
        raise WorkloadError(
            f"{source}: the model takes {shapes['W1'][1]} features and its data "
            f"recipe produces {recipe.get('features')} — one of the two is not "
            f"from this run"
        )
    for row in params["W1"] + params["W2"]:
        for value in row:
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise WorkloadError(f"{source}: model contains a non-numeric weight")
    return document


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    artifacts = read_artifacts(Path(args.artifacts))
    key, url = select_object(artifacts["objects"], args.model_key)
    print(f"▶ artifacts.json: job {artifacts.get('job_id')} — "
          f"{len(artifacts['objects'])} object(s), evaluating {key}", flush=True)

    fetch_started = time.monotonic()
    raw = fetch(url)
    fetch_ms = (time.monotonic() - fetch_started) * 1000.0
    model_sha256 = sha256_hex(raw)
    print(f"▶ fetched {len(raw)} bytes in {fetch_ms:.0f} ms — sha256 {model_sha256}",
          flush=True)

    expected = args.expect_sha256.strip().lower()
    if expected and expected != model_sha256:
        # Before parsing, so a substituted model is refused on identity alone
        # and never gets as far as being interpreted.
        raise WorkloadError(
            f"model sha256 {model_sha256} does not match the expected "
            f"{expected} — refusing to score a model that is not the one this "
            f"evaluation is about"
        )

    model = load_model(raw, f"model {key}")
    if args.save_model:
        Path(args.save_model).write_bytes(raw)

    recipe = model["data_recipe"]
    xs, ys = synth_dataset(recipe, "holdout")

    scored_started = time.monotonic()
    loss, accuracy = evaluate_params(model["params"], xs, ys)
    scored_ms = (time.monotonic() - scored_started) * 1000.0

    per_class: dict[str, int] = {}
    for label in ys:
        name = str(label)
        per_class[name] = per_class.get(name, 0) + 1

    metrics = {
        "schema": EVAL_METRICS_SCHEMA,
        "workload": "evaluate_model",
        "accuracy": accuracy,
        "loss": loss,
        "samples": len(xs),
        "samples_per_class": per_class,
        # The evidence. Equal to the training job's `model_sha256` iff this is
        # the model that run produced.
        "model_sha256": model_sha256,
        "model_key": key,
        "model_bytes": len(raw),
        "model_schema": model["schema"],
        "trained_for": model.get("trained_for", {}),
        # Provenance carried straight through from the orchestrator's
        # document, so the evaluation result names the training job and the
        # sandbox session it belongs to without this workload knowing anything
        # about either.
        "training_job_id": artifacts.get("job_id"),
        "session_id": artifacts.get("session_id"),
        "artifacts_issued_at": artifacts.get("issued_at"),
        "fetch_latency_ms": round(fetch_ms, 3),
        "evaluation_latency_ms": round(scored_ms, 3),
        "latency_ms": round(fetch_ms + scored_ms, 3),
    }
    atomic_write_json(out / "metrics.json", metrics)
    print(f"▶ accuracy {accuracy:.4f} on {len(xs)} held-out samples "
          f"({scored_ms:.0f} ms)", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except WorkloadError as exc:
        print(f"evaluate_model: {exc}", file=sys.stderr, flush=True)
        raise SystemExit(2) from None
