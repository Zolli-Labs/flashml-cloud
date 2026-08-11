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
from collections.abc import Sequence
from typing import Any

from flashruntime.images import manifest_for
from flashruntime.protocol.v1alpha1 import JobSpec

from flashml_cloud_api.datasets import Manifest
from flashml_cloud_api.elastic import dataset_chunks
from flashml_cloud_api.flashml_yaml import (
    SPLIT_REPLICA,
    SPLIT_SHARD,
    FlashmlConfig,
)
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

#: The workload parameter carrying the job's fully resolved pip requirement
#: lines — the image's own base manifest followed by the job's declared
#: extras. See ``_dependencies`` for the resolution rule and why the two
#: pieces must never be reordered.
DEPENDENCIES_PARAM = "dependencies"

#: The workload parameter carrying the job's declared extras ALONE, with the
#: base manifest excluded. This is what the coordinator's placement gate
#: (``IsolationAwarePlacement``, the eighth gate) keys on: a container host
#: already has the image's base manifest baked in — that is what "container
#: host" means — so it can never be missing the base, only ever the extras.
#: Keying the gate on ``DEPENDENCIES_PARAM`` instead would refuse ordinary
#: pytorch/sklearn jobs on the container hosts that run them correctly today,
#: because the base (`torch==2.3.1` and friends) would read as a requirement
#: the host cannot satisfy. See ``_dependencies``.
EXTRA_DEPENDENCIES_PARAM = "extra_dependencies"

#: The workload parameter carrying each task's share of every declared
#: dataset: one list per task, each a list of
#: ``{"name", "split", "entries": [{"path", "url", "size", "integrity"}]}``.
#:
#: Per-TASK and not per-job, unlike every other parameter in this module.
#: That is the whole design: the slicing decision is made once, here, at
#: submit time, where the manifest is in scope and the fleet's shape is
#: known — rather than by each host from a rule it would have to be told and
#: trusted to apply identically. A host is handed a list of URLs and fetches
#: them; it never computes which ones are its own.
#:
#: Deliberately **not** an entry in ``inputs``, for the same reason
#: ``LOCAL_INPUTS_PARAM`` is not: ``inputs`` values are ``artifact://`` URIs
#: for bytes that were uploaded to the control plane, and no dataset byte
#: ever touches our infrastructure. The URLs point at the submitter's own
#: origin and the host fetches them directly.
DATASET_SLICES_PARAM = "dataset_slices"

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


def _dependencies(
    config: FlashmlConfig, image: CuratedImage, parameters: dict[str, Any]
) -> None:
    """Resolve the job's dependency list: the image's own base manifest,
    then the job's declared extras.

    The base comes from ``image.reference`` — the resolved, fully pinned
    reference, not ``config.image`` (the alias or string a submitter wrote in
    flashml.yaml) — because it is ``manifest_for``'s key: "the requirement
    lines for a curated image, shipped in flashruntime as package data next
    to the Dockerfile that installs the same file. An unsandboxed host then
    installs what the container would have contained, rather than a second
    list someone maintained by hand. Extras are appended AFTER the base:
    a base manifest's ``--index-url`` line governs the lines that follow it
    (a CPU torch build versus a several-GB CUDA one), so putting an extra
    first would put it outside that index's effect.

    A custom (non-curated) image with no declared extras is refused HERE, at
    submit time, naming the image and why. The alternative is a trusted host
    claiming the task, failing to reproduce anything, and reporting it three
    hops later — an opaque node-side failure for something the submitter
    could have been told immediately.

    Emits TWO keys. ``DEPENDENCIES_PARAM`` is base + extras, unchanged — the
    full install list a no-container host materialises. ``EXTRA_DEPENDENCIES_
    PARAM`` is the extras ALONE — what the coordinator's placement gate
    reads, because a container host already has the base baked into its
    image and can never be missing it, only ever the extras. Both follow the
    same absent-stays-absent rule; conflating "empty" with "absent" for
    either key would either refuse python-slim jobs (see the ``is None``
    check below) or, for ``extra_dependencies`` specifically, wrongly lock
    ordinary curated-image jobs with no extras out of container hosts.
    """
    base = manifest_for(image.reference)
    extras = list(config.dependencies)
    # `is None`, NEVER truthiness. `manifest_for` returns `[]` for a CURATED
    # image that genuinely installs nothing beyond its base (python-slim)
    # and `None` for a reference it does not recognise at all. `if not base`
    # would conflate the two and refuse every python-slim job at submit time.
    if base is None and not extras:
        raise CompileError(
            f"image {config.image!r} is not a curated FlashML image and the "
            f"job declares no 'dependencies:' — an unsandboxed host cannot "
            f"reproduce its environment. Either use a curated image or list "
            f"what the job needs."
        )
    resolved = (base or []) + extras
    # Absent, never `[]` — the same judgement `_local_inputs` already
    # records: every job deployed today (no `dependencies:`, a curated image
    # with an empty manifest) resolves to an empty list, and that path must
    # stay byte-identical rather than gain a payload key nothing reads.
    if resolved:
        parameters[DEPENDENCIES_PARAM] = resolved
    # The extras ALONE — never `resolved`, or a container host with the
    # base already baked in would be refused for a requirement it already
    # satisfies. Absent when empty, same rule as above: a curated image with
    # no declared extras (the overwhelming common case) must not gain a
    # payload key that would route it away from container hosts it runs on
    # correctly today.
    if extras:
        parameters[EXTRA_DEPENDENCIES_PARAM] = extras


def _dataset_slices(
    config: FlashmlConfig,
    manifests: dict[str, Manifest] | None,
    parameters: dict[str, Any],
    *,
    chunk_ids: Sequence[int],
    total_chunks: int,
) -> None:
    """Cut every declared dataset into one slice per task.

    ``split`` is inferred from ``mode`` when the file does not say:
    federated means disjoint slices whose union is one pass, and anything
    else means each task needs the whole dataset. An explicit ``split:``
    wins — the inference is a default, not a rule, and it is overridable in
    both directions.

    ``chunk_ids`` is one chunk id per task, in task order, and
    ``total_chunks`` is how many chunks the whole pass is cut into. For a
    sweep the two are the same thing (``range(n)`` over ``n`` tasks) and the
    distinction costs nothing; for a federated round it is load-bearing and
    the manifest is cut against ``total_chunks``. **Cutting against the
    number of slots instead would be wrong in exactly the rounds that
    matter**: a round with two slots online and a four-chunk pass would hand
    each of them half the dataset while its argv said ``--num-shards 4``, so
    the driver would credit a swept pass that never happened and the next
    round would retrain the same bytes. A slot's chunk id is also not its
    index — ``fedavg.slot_chunks_for`` rotates them through the pass — so
    the slice follows the id, which is the same integer ``--shard`` carries
    and ``chunks_done`` reports.

    Absent stays absent, the same judgement ``_local_inputs`` records.
    """
    if not config.datasets:
        return
    # NOT `or not manifests`. Declared-but-unresolved must fail LOUD: an
    # early return here emits a job whose tasks fetch nothing, run against
    # an empty /work/data/, and fail on a missing path — or worse, train on
    # whatever the entrypoint falls back to. Same "does not fail closed"
    # shape the payload forwards in `recipes/command.py` are all commented
    # against. The per-dataset `manifest is None` check below cannot save us
    # if we return before reaching it.
    if not manifests:
        raise CompileError(
            f"job declares {len(config.datasets)} dataset(s) but none were "
            f"resolved — refusing to compile a job that would run with no data"
        )
    default_split = SPLIT_SHARD if config.is_federated else SPLIT_REPLICA
    chunk_ids = list(chunk_ids)
    per_task: list[list[dict[str, Any]]] = [[] for _ in chunk_ids]
    for declared in config.datasets:
        manifest = manifests.get(declared["name"])
        if manifest is None:
            raise CompileError(
                f"dataset {declared['name']!r} was declared but not resolved — "
                f"refusing to compile a job whose tasks would look for it in an "
                f"empty /work/data/{declared['name']}/"
            )
        split = declared.get("split") or default_split
        # `dict(e.integrity)` so a slice never aliases the manifest's own
        # dict; the manifest is frozen but that field is not.
        entries = [
            {"path": e.path, "url": e.url, "size": e.size,
             "integrity": dict(e.integrity)}
            for e in manifest.entries
        ]
        if split == SPLIT_REPLICA:
            for slot in range(len(chunk_ids)):
                per_task[slot].append(
                    {"name": manifest.name, "split": split,
                     "entries": list(entries)}
                )
            continue
        # Against `total_chunks`, indexed by chunk id — see the docstring.
        groups = dataset_chunks([e.size for e in manifest.entries], total_chunks)
        for slot, chunk_id in enumerate(chunk_ids):
            per_task[slot].append({
                "name": manifest.name,
                "split": split,
                "entries": [entries[i] for i in groups[chunk_id]],
            })
    parameters[DATASET_SLICES_PARAM] = per_task


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
    manifests: dict[str, Manifest] | None = None,
) -> dict[str, Any]:
    """Compile a validated config into the JobSpec dict the coordinator takes.

    ``code_artifact_uri`` is the ``artifact://`` URI the repo tarball was
    staged at; it becomes the ``code`` input the executor downloads to
    ``/work/inputs/`` before the command runs.

    ``pool`` is the one exception to "fixed and not configurable" — see the
    ``isolation``/``placement`` lines below.

    ``manifests`` maps a declared dataset's ``name`` to the pinned manifest
    the route resolved for it. Resolution is a network call and belongs to
    the caller, not here; this function only cuts what it is handed. A
    config that declares datasets and is given none is REFUSED — see
    ``_dataset_slices``.
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
    _dependencies(config, image, parameters)
    # One chunk per task and one task per chunk: an independent job has no
    # pass to sweep and no rotation to honour, so the two numbers a
    # federated round keeps apart collapse into one here. `or 1` because a
    # job with no sweep is one task, not zero.
    _task_count = len(task_params) if task_params else 1
    _dataset_slices(config, manifests, parameters,
                    chunk_ids=range(_task_count), total_chunks=_task_count)

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
    slot_chunks: Sequence[int],
    total_chunks: int,
    pool: str | None = None,
    manifests: dict[str, Manifest] | None = None,
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
    - one task per **slot**, each told the chunk of the data it is to train
      on, so the slots partition the pass rather than each training on all
      of it.
    - if the job declares ``datasets:``, that same chunk id also selects the
      slot's share of each pinned manifest (``manifests``, keyed by declared
      name). The integers in argv and the files in the slice are cut from
      one layout, which is what keeps ``chunks_done`` describing the bytes
      the machine actually trained on.

    ``slot_chunks`` and ``total_chunks`` are handed in rather than read off
    the config, because neither is the author's to state: the pass is cut
    from the machines online when the round was submitted, and which chunk
    each slot starts at depends on where the previous round's coverage ended.

    **A slot's chunk id is not its index**, and this module does not compute
    it. ``fedavg.slot_chunks_for`` does, using the runtime's own
    ``chunks.slot_start`` — because ``run_fedavg`` verifies every chunk a
    machine reports against exactly that call, and a round compiled with any
    other layout has its contributions credited zero: the machines train, the
    volunteers spend their electricity, and the round reduces nothing. This
    module only serialises the list it is given, which is what keeps the
    runtime import one door wide (``tests/test_import_boundary.py``).

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

    slot_chunks = list(slot_chunks)
    if not slot_chunks:
        raise CompileError("a federated round needs at least one slot")
    if total_chunks < 1:
        raise CompileError(
            f"a federated round needs at least one chunk, got {total_chunks}"
        )
    outside = [c for c in slot_chunks if not 0 <= c < total_chunks]
    if outside:
        raise CompileError(
            f"slot chunk id(s) {outside!r} are outside the pass, which has "
            f"{total_chunks} chunk(s) — a task told to train a chunk that does "
            f"not exist is a contribution nothing will credit"
        )

    entry = _entrypoint_path(config.entrypoint)

    # `--shard` is the only per-task value, so it is the only placeholder;
    # every other token is escaped because CommandRecipe runs str.format over
    # all of them (see _escape_braces).
    #
    # `--num-shards` is the chunk count of a whole PASS, not the number of
    # machines: the user's `shard_of(x, y, shard, num_shards)` strides the
    # data by it, so it must be the number the chunk ids were minted from or
    # a task trains a slice that overlaps its neighbours'.
    #
    # BOTH STAY when the job declares `datasets:` (spec §14.4), even though
    # the slicing has already happened by then and the task's files are
    # chosen for it. They are no longer only a slicing instruction: the
    # worker reports `chunks_done: [args.shard]`, and `run_fedavg` averages
    # a contribution that names no chunk in with zero weight. Dropping them
    # would therefore credit every machine in every federated dataset job
    # nothing at all, while every round looked healthy — the machines train,
    # the volunteers spend their electricity, and the round reduces nothing.
    fixed = ["python", entry, *config.args,
             "--round", str(round_index),
             "--num-shards", str(total_chunks)]
    command = [_escape_braces(token) for token in fixed] + ["--shard", "{shard}"]
    task_params = [{"shard": str(chunk)} for chunk in slot_chunks]

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
    # A federated round is an ordinary command job (see this function's
    # docstring); the same base-plus-extras resolution applies to it.
    _dependencies(config, image, parameters)
    # The pass, not the round, is what a declared dataset is cut against:
    # `total_chunks` chunks, of which this round's slots claim
    # `slot_chunks`. Passing `len(slot_chunks)` here would silently hand a
    # narrow round the whole dataset — see `_dataset_slices`.
    _dataset_slices(config, manifests, parameters,
                    chunk_ids=slot_chunks, total_chunks=total_chunks)

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
