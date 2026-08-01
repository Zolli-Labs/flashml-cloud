"""``flashml.yaml`` + a staged code artifact → a coordinator ``JobSpec``.

The output is a ``workload.type: "command"`` spec, consumed upstream by
``CommandRecipe`` (``flashruntime/recipes/command.py``). Two properties of
that recipe shape everything here:

- ``command`` is an **argv list of strings**, never a shell string. There is
  no shell on the far side: ``ArgvDockerRunner`` hands the list straight to
  ``docker run <image> *argv``. Quoting, ``&&``, globbing and redirection do
  not exist, which is why this module never builds a command by joining
  strings.
- ``inputs`` values must be ``artifact://`` URIs. The repo tarball is staged
  as one, and the executor downloads declared inputs to ``/work/inputs/``
  before the command starts — that staging, not an image build, is how a
  user's own code reaches a volunteer node (see the plan's "Why no image
  build").

**Isolation is fixed at ``sandboxed`` with ``allowFallback: false``, and
that is not configurable from ``flashml.yaml``.** ``CommandRecipe`` refuses
any other tier for a command job and rejects the ``allowFallback`` waiver
outright: a submitter can never downgrade the isolation their own arbitrary
code runs under. This module does not expose a knob for it, does not read
one from the config, and does not try — the only correct value is the one
the recipe would accept anyway.

The compiled spec is validated through the real ``JobSpec`` model before it
is returned, so a spec this module could not have built correctly fails
here, in the API, rather than as a 422 from the coordinator that the user
would see as an opaque platform error.
"""
from __future__ import annotations

import itertools
import json
import re
from typing import Any

from flashruntime.protocol.v1alpha1 import JobSpec

from flashml_cloud_api.flashml_yaml import FlashmlConfig
from flashml_cloud_api.images import CuratedImage

#: Where the executor stages declared inputs, and where a task's collected
#: output must land. Both are the flashnode executor's contract, not a
#: choice made here.
INPUTS_DIR = "/work/inputs"
OUT_DIR = "/work/out"

#: The input name the repo tarball is staged under.
CODE_INPUT = "code"

#: A sweep key becomes both a ``--flag`` and a ``str.format`` field name, so
#: it has to be a plain identifier: ``"{a-b}".format(**{"a-b": 1})`` raises
#: inside the recipe, which would surface as a 500 rather than as the user
#: error it is.
_SWEEP_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

#: A job name must be a DNS-1123 label upstream (``JobMetadata.name``).
_NAME_ALLOWED = re.compile(r"[^a-z0-9-]+")

MAX_NAME_LENGTH = 63


class CompileError(Exception):
    """Raised for a config that parses but cannot become a valid JobSpec.

    Distinct from ``ConfigError`` on purpose: this is the layer that knows
    about argv, sweeps and the upstream schema, and its messages name the
    thing the user has to change.
    """


def sanitize_job_name(name: str) -> str:
    """Coerce a user's ``name:`` into a DNS-1123 label.

    Upstream ``JobMetadata`` refuses anything else, and a user's job name is
    free text. Coercing beats refusing here: "CIFAR Sweep #2" is a perfectly
    reasonable thing to call a job, and turning it into ``cifar-sweep-2`` is
    what the user meant.
    """
    lowered = _NAME_ALLOWED.sub("-", str(name).strip().lower())
    trimmed = lowered.strip("-")[:MAX_NAME_LENGTH].strip("-")
    return trimmed or "job"


def _entrypoint_path(entrypoint: str) -> str:
    """The entrypoint as the container will see it.

    ``preflight`` has already refused an entrypoint that escapes the repo,
    but this runs on the same untrusted string and must not be the place a
    leading ``/`` or a ``..`` slips into argv, so it normalises
    independently rather than trusting the earlier check.
    """
    cleaned = entrypoint.strip().lstrip("/")
    parts = [p for p in cleaned.split("/") if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts):
        raise CompileError(f"entrypoint {entrypoint!r} is not a path inside the repo")
    return f"{INPUTS_DIR}/{CODE_INPUT}/" + "/".join(parts)


def _escape_braces(token: str) -> str:
    """Make a literal argv token survive ``str.format``.

    ``CommandRecipe`` calls ``token.format(**params)`` on **every** token of
    a sweep's command, not only the ones with placeholders. So a user arg
    that legitimately contains a brace — ``--filter={"a":1}`` — would raise
    a KeyError inside the recipe and surface as a failed submission the user
    cannot explain. Doubling the braces here makes it come back out as
    itself.
    """
    return token.replace("{", "{{").replace("}", "}}")


def _sweep_axes(sweep: dict[str, list]) -> list[tuple[str, list]]:
    axes: list[tuple[str, list]] = []
    for key, values in sweep.items():
        if not _SWEEP_KEY_RE.match(str(key)):
            raise CompileError(
                f"sweep key {key!r} must be a plain identifier (letters, digits "
                f"and underscore, not starting with a digit) — it becomes both a "
                f"--flag and a substitution field"
            )
        axes.append((str(key), list(values)))
    return axes


def _stringify(value: Any) -> str:
    """Render a sweep value as the argv token the script will parse.

    ``True``/``False`` before the ``int`` branch on purpose: in Python a
    bool *is* an int, and a sweep value of ``true`` becoming ``1`` on the
    command line is a silent wrong answer.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float, str)):
        return str(value)
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def _resources(config: FlashmlConfig) -> dict[str, Any]:
    raw = config.resources or {}
    resources: dict[str, Any] = {}
    cpus = raw.get("cpus")
    if cpus is not None:
        if isinstance(cpus, bool) or not isinstance(cpus, (int, float)) or cpus <= 0:
            raise CompileError(f"resources.cpus must be a positive number, got {cpus!r}")
        resources["cpuPerTask"] = float(cpus)
    memory_gb = raw.get("memory_gb")
    if memory_gb is not None:
        if (
            isinstance(memory_gb, bool)
            or not isinstance(memory_gb, (int, float))
            or memory_gb <= 0
        ):
            raise CompileError(
                f"resources.memory_gb must be a positive number, got {memory_gb!r}"
            )
        resources["memoryPerTask"] = f"{int(round(memory_gb * 1024))}Mi"
    return resources


def _split_reference(reference: str) -> tuple[str, str]:
    """``docker.io/library/python:3.11.9-slim`` → repository and tag.

    Split on the last colon, but only if it is after the last slash: a
    registry may carry a port (``registry:5000/img``), and splitting on that
    colon would produce a repository of ``registry`` and a tag containing a
    path.
    """
    head, sep, tail = reference.rpartition(":")
    if not sep or "/" in tail:
        raise CompileError(
            f"curated image reference {reference!r} is not fully pinned "
            f"(expected repository:tag)"
        )
    return head, tail


def compile_to_jobspec(
    config: FlashmlConfig,
    image: CuratedImage,
    code_artifact_uri: str,
    job_name: str,
) -> dict[str, Any]:
    """Compile a validated config into the JobSpec dict the coordinator takes.

    ``code_artifact_uri`` is the ``artifact://`` URI the repo tarball was
    staged at; it becomes the ``code`` input the executor downloads to
    ``/work/inputs/`` before the command runs.
    """
    if not str(code_artifact_uri).startswith("artifact://"):
        # CommandRecipe.validate_params refuses anything else; catching it
        # here keeps the error attributable to this module rather than to a
        # 422 from the coordinator.
        raise CompileError(
            f"code_artifact_uri must be an artifact:// URI, got {code_artifact_uri!r}"
        )

    entry = _entrypoint_path(config.entrypoint)
    axes = _sweep_axes(config.sweep)

    command: list[str] = ["python", entry, *config.args]
    task_params: list[dict[str, Any]] | None = None

    if axes:
        # Every token is run through str.format upstream, so the fixed part
        # of the command has to be escaped before the placeholders are added.
        command = [_escape_braces(token) for token in command]
        for key, _values in axes:
            command += [f"--{key}", "{" + key + "}"]
        task_params = [
            {key: _stringify(value) for key, value in zip([k for k, _ in axes], combo)}
            for combo in itertools.product(*[values for _, values in axes])
        ]

    parameters: dict[str, Any] = {
        "command": command,
        "inputs": {CODE_INPUT: code_artifact_uri},
        "env": {},
    }
    if task_params is not None:
        parameters["task_params"] = task_params
    if config.timeout_seconds is not None:
        parameters["timeout_seconds"] = config.timeout_seconds

    repository, tag = _split_reference(image.reference)

    spec: dict[str, Any] = {
        "apiVersion": "flashml.dev/v1alpha1",
        "kind": "Job",
        "metadata": {
            "name": sanitize_job_name(job_name),
            "labels": {"flashml.dev/source": "github-repo"},
        },
        "spec": {
            "execution": {"backend": "leases", "environment": "auto"},
            "image": {"repository": repository, "tag": tag},
            "workload": {"type": "command", "parameters": parameters},
            "resources": _resources(config),
            # Fixed, and deliberately not configurable — see the module
            # docstring. CommandRecipe refuses any other tier and rejects
            # the allowFallback waiver; a submitter must never be able to
            # choose where their own arbitrary code runs unsandboxed.
            "isolation": {"tier": "sandboxed", "allowFallback": False},
            "artifacts": {"outputPrefix": "artifact://jobs/{job_id}/"},
        },
    }

    try:
        validated = JobSpec.model_validate(spec)
    except Exception as exc:  # pydantic ValidationError and anything under it
        raise CompileError(f"compiled JobSpec is invalid: {exc}") from None
    return json.loads(validated.model_dump_json())
