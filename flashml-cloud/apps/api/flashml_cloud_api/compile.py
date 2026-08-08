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

**Isolation is fixed at ``sandboxed``, and that is not configurable from
``flashml.yaml``.** ``CommandRecipe`` refuses any other tier for a command
job: a submitter can never downgrade the isolation their own arbitrary code
runs under. This module does not expose a knob for it, does not read one
from the config, and does not try — the only correct value is the one the
recipe would accept anyway.

The one caller-settable exception is ``allowFallback``, and it is not a
``flashml.yaml`` key either — it is the ``pool`` keyword both compilers
below take, threaded in from the API route after ``fetch_pool_for_member``
has confirmed the submitter belongs to the pool named. ``CommandRecipe``
refuses the waiver on its own unless ``placement.pool`` is also set, so the
two are coupled bidirectionally: allowFallback iff pool, enforced upstream
and pinned by tests here both ways.

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

#: The input name each federated round's aggregated weights are staged
#: under. Note the *file* a task actually sees is named after the artifact
#: key's basename, not after this name — flashnode downloads a non-unpacked
#: input to ``inputs/<basename of key>`` (``executor/loop.py``) — and the
#: driver writes its weights to ``…/round-NNN/weights.json``. So the path
#: the user's code opens is ``WEIGHTS_PATH`` below, and these two constants
#: are one contract: change the driver's key and this goes with it.
WEIGHTS_INPUT = "weights"
WEIGHTS_PATH = f"{INPUTS_DIR}/weights.json"

#: The file a federated task writes its weight change to, inside OUT_DIR.
#: ``fedavg_driver`` reads the name from each task's ``metrics.json``
#: (``delta_file``) and defaults to exactly this.
DELTA_FILE = "delta.json"

#: The workload parameter carrying the host-supplied dataset labels.
#:
#: Deliberately **not** an entry in ``inputs``. Everything in ``inputs`` is an
#: ``artifact://`` URI that the executor downloads, which means bytes that
#: were uploaded to the control plane first. A local input is the opposite of
#: that and by design: the directory stays on the host owner's machine, the
#: agent bind-mounts it read-only at ``inputs/<label>/``, and nothing about it
#: ever crosses the network. Adding a label to ``inputs`` would declare an
#: artifact that does not and must not exist; adding it to ``unpack_inputs``
#: would point an archive extractor at that non-existent download.
#:
#: The name matches what the coordinator's placement gate reads
#: (``IsolationAwarePlacement``, ``task.payload["local_inputs"]``) and what
#: flashnode's runner reads when building the mount. One string, three
#: readers — see the caveat in ``_local_inputs`` about the middle step.
LOCAL_INPUTS_PARAM = "local_inputs"

#: A federated round's lease has to outlive local training, not a single
#: HTTP call — ``CommandRecipe``'s 60 s default would expire mid-epoch and
#: hand the shard to a second machine while the first was still working.
DEFAULT_FEDERATED_LEASE_SECONDS = 600.0

#: Upstream (``service/modea.MAX_LEASE_SECONDS``) refuses more than an hour
#: for the built-in expansion; ``CommandRecipe`` does not clamp at all, so
#: this module must, or a repo's ``timeout_seconds: 86400`` would pin a
#: shard to one machine for a day.
MAX_LEASE_SECONDS = 3600.0

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


def _local_inputs(config: FlashmlConfig, parameters: dict[str, Any]) -> None:
    """Attach the config's local dataset labels to a workload's parameters.

    Absent stays absent rather than becoming ``[]`` — the judgement
    ``CommandRecipe`` already records for ``unpack_inputs``. The placement
    gate reads ``payload.get("local_inputs")`` and short-circuits on ``None``,
    so an omitted key leaves every pre-existing job's placement path byte for
    byte what it was, and keeps that path exercised.

    The labels are not re-validated here: ``parse_flashml_yaml`` already
    refused anything outside the shared alphabet, and a second, subtly
    different rule in a second module is how the two drift apart.

    **Known gap, verified against the pinned runtime rather than assumed:**
    ``CommandRecipe.expand`` builds each task payload from a fixed key list
    and does not forward unrecognised workload parameters, so this value stops
    at the JobSpec and does not yet reach ``task.payload``. That forwarding is
    a one-line change in the public repo and is not this repo's to make (hard
    rule 2). Until it lands, the gate sees an absent capability requirement,
    fails open for local-data jobs, and flashnode mounts nothing — the
    submitter's half of the contract is complete and correct, and the tasks
    are simply not yet routed by it.
    """
    if config.local_inputs:
        parameters[LOCAL_INPUTS_PARAM] = list(config.local_inputs)


def _resources(config: FlashmlConfig) -> dict[str, Any]:
    """``flashml.yaml resources:`` → the upstream ``ResourcesSpec`` fields.

    **Known gap, verified against the pinned runtime rather than assumed:**
    ``gpuPerTask`` is emitted here, but ``ResourcesSpec`` in the pinned
    flashruntime (0.4.0) does not declare it. ``compile_to_jobspec`` returns
    ``JobSpec.model_validate(spec).model_dump_json()``, and pydantic's default
    ``extra="ignore"`` **drops the field silently** — no error, no warning, a
    ``gpus: 1`` job that compiles to a CPU spec. The field lands upstream in
    flashruntime 0.5.0; re-pinning is plan Task 10, and this repo cannot make
    that change (hard rule 2).

    So this validation is the submitter's half of the contract, complete and
    correct, and the value simply does not yet reach the coordinator.
    ``tests/test_compile.py`` asserts the emission here directly and gates the
    round-trip assertion on the pin, so the day the pin moves the coverage
    turns on by itself.
    """
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
    gpus = raw.get("gpus")
    if gpus is not None:
        # Non-negative, not positive, and int-only — deliberately unlike
        # ``cpus``/``memory_gb`` above:
        #
        # * ``0`` is the meaningful default (``ResourcesSpec.gpuPerTask``
        #   defaults to 0), so a user writing it out is not making an error.
        # * ``bool`` is an ``int`` subclass, so ``gpus: true`` would otherwise
        #   read as "one GPU" and route a job to a device the user never asked
        #   for. Refused, not coerced.
        # * A ``float`` is refused rather than rounded. The placement gate
        #   upstream fails closed on a non-``int`` requirement, so emitting
        #   ``1.5`` — or silently rounding it to ``2`` — produces a job that is
        #   either unplaceable everywhere or placed against a count the user
        #   never wrote. Better to say so at submit time.
        if isinstance(gpus, bool) or not isinstance(gpus, int) or gpus < 0:
            raise CompileError(
                f"resources.gpus must be a non-negative integer, got {gpus!r}"
            )
        resources["gpuPerTask"] = int(gpus)
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
    *,
    pool: str | None = None,
) -> dict[str, Any]:
    """Compile a validated config into the JobSpec dict the coordinator takes.

    ``code_artifact_uri`` is the ``artifact://`` URI the repo tarball was
    staged at; it becomes the ``code`` input the executor downloads to
    ``/work/inputs/`` before the command runs.

    ``pool`` is the one exception to "fixed and not configurable" — see the
    ``isolation``/``placement`` lines below.
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
        # The staged artifact is the repo *tarball*. Without this the
        # executor drops it on disk as a single file and the argv above
        # looks for `<entrypoint>` inside a gzip blob — "file not found",
        # every repo job, which is exactly how this shipped once.
        # `unpack_inputs` is the executor's explicit opt-in: it extracts
        # this input to `/work/inputs/code/` (stripping GitHub's
        # `owner-name-<sha>/` wrapper) and hands the runner that directory,
        # which is the path `_entrypoint_path` builds argv against. The two
        # are one contract; changing either alone breaks the job.
        "unpack_inputs": [CODE_INPUT],
        "env": {},
    }
    if task_params is not None:
        parameters["task_params"] = task_params
    if config.timeout_seconds is not None:
        parameters["timeout_seconds"] = config.timeout_seconds
    # Forwarded VERBATIM. The coordinator owns their semantics — which
    # reducers exist, how a range splits into shards, what a schema means —
    # and expanding or re-validating them here would be a second copy of
    # rules that already have an owner. Absent stays absent, never an empty
    # dict, so the no-declaration path keeps being the one exercised.
    if config.partition:
        parameters["partition"] = dict(config.partition)
    if config.validators:
        parameters["validators"] = dict(config.validators)
    if config.reduce:
        parameters["reduce"] = dict(config.reduce)
    _local_inputs(config, parameters)

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
            # A `retryPolicy` field, not a workload parameter: the
            # coordinator derives job state from `retryPolicy.allowPartial`,
            # and putting it in `parameters` would file it where nothing
            # reads it. Emitted only when asked for, so the default stays
            # whatever the protocol's own default is.
            **({"retryPolicy": {"allowPartial": True}} if config.allow_partial else {}),
            # The one exception to "fixed and not configurable": a POOL job
            # carries the waiver, because the seventh placement gate confines
            # it to machines whose owners joined the submitter's team. The
            # invariant is bidirectional and pinned by tests both ways:
            # allowFallback iff pool. CommandRecipe enforces the same rule
            # upstream, so a spec that violates it cannot even expand.
            "isolation": {"tier": "sandboxed", "allowFallback": pool is not None},
            "placement": {"pool": pool if pool is not None else "any"},
            "artifacts": {"outputPrefix": "artifact://jobs/{job_id}/"},
        },
    }

    try:
        validated = JobSpec.model_validate(spec)
    except Exception as exc:  # pydantic ValidationError and anything under it
        raise CompileError(f"compiled JobSpec is invalid: {exc}") from None
    return json.loads(validated.model_dump_json())


# ---------------------------------------------------------------------------
# federated rounds
# ---------------------------------------------------------------------------


def federated_task_ids(shards: int) -> list[str]:
    """The task ids ``CommandRecipe`` will produce for a round of ``shards``.

    Stated here rather than inferred by the driver: the driver's
    participant count is filtered against exactly this set (an artifact key
    outside it would mint a participant out of nothing), so the naming rule
    has to come from the module that decides how the round is compiled.
    Mirrors ``CommandRecipe.expand``'s ``task-{i:03d}``.
    """
    return [f"task-{i:03d}" for i in range(shards)]


def compile_federated_round(
    config: FlashmlConfig,
    image: CuratedImage,
    code_artifact_uri: str,
    job_name: str,
    *,
    round_index: int,
    weights_uri: str | None,
    pool: str | None = None,
) -> dict[str, Any]:
    """Compile **one round** of a ``mode: federated`` config into a JobSpec.

    A federated run is N of these, chained by ``fedavg_driver.run_fedavg``
    running inside this API (design spec §5.4.5 — aggregation stays on
    trusted infrastructure because until result verification exists a node's
    reported numbers are believed).

    The round is an ordinary ``command`` job, which is the whole point: the
    round worker is the *user's own entrypoint*, not a built-in module, so
    everything already true of a repo job (sandboxed isolation, staged code,
    ``--network none``, output collected from ``/work/out``) is true here
    unchanged. What makes it federated is only what is added:

    - ``weights_uri`` staged as an input, so the round's aggregated weights
      arrive at ``WEIGHTS_PATH``. ``None`` on round 0 — there is nothing to
      broadcast yet, and the input is omitted rather than pointed at an
      empty artifact, so the user's code can test for the file's absence.
    - one task per shard, each told its ``--shard`` index, so the shards
      partition the data rather than each training on all of it.

    The user's side of the contract (``delta.json`` + a ``metrics.json``
    carrying ``samples`` and ``loss``) is not enforceable from here — it is
    checked statically by ``preflight``'s ``federated-contract`` rule and
    would otherwise fail at commit time on a volunteer's machine.
    """
    if not config.is_federated:
        raise CompileError(
            f"compile_federated_round needs a 'mode: federated' config, got "
            f"{config.mode!r}"
        )
    if not str(code_artifact_uri).startswith("artifact://"):
        raise CompileError(
            f"code_artifact_uri must be an artifact:// URI, got {code_artifact_uri!r}"
        )
    if weights_uri is not None and not str(weights_uri).startswith("artifact://"):
        raise CompileError(
            f"weights_uri must be an artifact:// URI, got {weights_uri!r}"
        )
    if not isinstance(round_index, int) or isinstance(round_index, bool) or round_index < 0:
        raise CompileError(f"round_index must be a non-negative int, got {round_index!r}")

    shards = config.shards or 0
    if shards < 1:
        raise CompileError("a federated config must resolve to at least one shard")

    entry = _entrypoint_path(config.entrypoint)

    # `--shard` is the only per-task value, so it is the only placeholder;
    # every other token is escaped because CommandRecipe runs str.format over
    # all of them (see _escape_braces).
    fixed = ["python", entry, *config.args,
             "--round", str(round_index),
             "--num-shards", str(shards)]
    command = [_escape_braces(token) for token in fixed] + ["--shard", "{shard}"]
    task_params = [{"shard": str(i)} for i in range(shards)]

    inputs = {CODE_INPUT: code_artifact_uri}
    if weights_uri is not None:
        inputs[WEIGHTS_INPUT] = weights_uri

    lease_seconds = float(
        config.timeout_seconds
        if config.timeout_seconds is not None
        else DEFAULT_FEDERATED_LEASE_SECONDS
    )

    parameters: dict[str, Any] = {
        "command": command,
        "inputs": inputs,
        # Only the code tarball is an archive. The weights artifact is a
        # plain JSON document and must stay a file the task can open.
        "unpack_inputs": [CODE_INPUT],
        "env": {},
        "task_params": task_params,
        "lease_seconds": min(lease_seconds, MAX_LEASE_SECONDS),
    }
    if config.timeout_seconds is not None:
        parameters["timeout_seconds"] = config.timeout_seconds
    # Federated averaging over data that cannot be pooled is the use case this
    # feature exists for, so every round carries the requirement too.
    _local_inputs(config, parameters)

    repository, tag = _split_reference(image.reference)

    spec: dict[str, Any] = {
        "apiVersion": "flashml.dev/v1alpha1",
        "kind": "Job",
        "metadata": {
            # The round suffix is appended AFTER trimming the base name to
            # fit, not before: `sanitize_job_name` truncates at 63 and would
            # otherwise cut the suffix off a long name, making every round
            # of that run share one job name.
            "name": sanitize_job_name(
                sanitize_job_name(job_name)[: MAX_NAME_LENGTH - 5].rstrip("-")
                + f"-r{round_index:03d}"
            ),
            "labels": {
                "flashml.dev/source": "github-repo",
                "flashml.dev/mode": "federated",
            },
        },
        "spec": {
            "execution": {"backend": "leases", "environment": "auto"},
            "image": {"repository": repository, "tag": tag},
            "workload": {"type": "command", "parameters": parameters},
            "resources": _resources(config),
            # Same coupling as compile_to_jobspec — see its comment. A
            # federated round is an ordinary command job, and pool-scoping
            # changes what the tasks compute, not the rule that governs
            # where they may run unsandboxed.
            "isolation": {"tier": "sandboxed", "allowFallback": pool is not None},
            "placement": {"pool": pool if pool is not None else "any"},
            "artifacts": {"outputPrefix": "artifact://jobs/{job_id}/"},
        },
    }

    try:
        validated = JobSpec.model_validate(spec)
    except Exception as exc:
        raise CompileError(f"compiled JobSpec is invalid: {exc}") from None
    return json.loads(validated.model_dump_json())
