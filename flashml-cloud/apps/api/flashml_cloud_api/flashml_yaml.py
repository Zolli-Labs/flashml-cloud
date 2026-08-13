"""Parsing and validation for a repo's ``flashml.yaml``.

Pure text in, typed config out — no filesystem or network I/O, so the repo
fetch (Task 3) and preflight (Task 4) can each hand this raw bytes decoded
to text without this module knowing where they came from.

Every rule here is a deliberate refusal rather than a lenient guess:
malformed or ambiguous input should fail loudly with a message that names
the offending key, not be coerced into whatever the parser could make of
it. See the plan's Task 2 section for the rationale behind the two rules
worth calling out specifically: ``args`` is an argv list because there is
no shell to hand a string to, and unknown top-level keys are refused
(rather than ignored) so a typo like ``entrypint`` cannot silently mean
something the author didn't intend.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from fractions import Fraction
from math import ceil, prod

import yaml

#: Both schema versions this parser reads. Version 1 is every sweep and
#: independent config already in the wild and is not deprecated — the bump
#: exists only because ``mode: federated`` changed its authoring surface
#: (``FEDERATED_VERSION`` below), and a breaking change to a shipped config
#: needs a version the author can see rather than a field that silently
#: means something else.
SUPPORTED_VERSIONS = (1, 2)

#: The version ``mode: federated`` requires. A version-1 federated config
#: typed ``rounds``/``min_participants``/``shards``; there is no reading of
#: those three that this API can still honour, because the runtime it drives
#: no longer accepts a shard count or a machine quorum at all. So they are
#: refused with the migration rather than reinterpreted.
FEDERATED_VERSION = 2

#: What each removed federated key was replaced by, in the words a person
#: needs to fix their file. These are refused BEFORE the generic unknown-key
#: check, because "unknown key 'shards'" sends someone hunting for a typo in
#: a line they copied from our own documentation.
REMOVED_FEDERATED_KEYS = {
    "rounds": (
        "use 'epochs' instead — the total number of passes over your data. "
        "A round is no longer a fixed amount of training: 'sync_every' sets "
        "how often the machines combine what they learned, so the round "
        "count is derived (epochs / sync_every) and shown in the console "
        "rather than typed"
    ),
    "min_participants": (
        "removed with nothing in its place — a round now ends on data "
        "coverage, not on a machine count. Machines contribute unequally "
        "(a fast one completes more chunks than a slow one), so waiting for "
        "N machines stopped describing anything. Set 'epochs', and "
        "'sync_every' if you want more frequent combines"
    ),
    "shards": (
        "removed with nothing in its place — the fleet decides the split "
        "now. One pass over the data is cut into uniform chunks and each "
        "machine takes as many as it can finish, so nothing has to guess "
        "how many machines will be online. Set 'epochs' (and optionally "
        "'sync_every') instead"
    ),
}

#: Every key refused with a REASON rather than as a typo — the three removed
#: federated keys above, plus one that was never a key at all.
#:
#: ``checkpoint`` is here because someone who reads that FlashML survives a
#: machine dying will reasonably try to switch that on, and today they get
#: "unknown key(s) ['checkpoint']" and go hunting for a spelling mistake in a
#: feature that is not theirs to configure: it is unconditional, emitted on
#: every compiled job (see ``compile.CHECKPOINT_PARAM``). So the refusal has
#: to teach the convention — where to write state and where to read it back —
#: rather than merely say no.
#:
#: It is deliberately NOT added to ``ALLOWED_KEYS``. Accepting the key and
#: ignoring whatever it was set to is exactly the "surface that takes input it
#: does not honour" this module exists to refuse.
#:
#: ONE dict and ONE check, extended rather than paralleled: a second refusal
#: path with its own message shape is how two answers to the same file drift
#: into disagreeing.
REFUSED_KEYS = {
    **REMOVED_FEDERATED_KEYS,
    # Absolute paths, matching `preflight` and the authoring guide. Both
    # spellings work — the container runs with `-w /work` — but one product
    # must not say `out/ckpt/` here and `/work/out/ckpt/` two screens later,
    # and absolute is the one that survives a workload that chdir()s.
    "checkpoint": (
        "not a key — checkpointing is always on. Write your resumable state "
        "to /work/out/ckpt/step-<N>.json and read /work/inputs/resume.json "
        "on start; FlashML relays those files as your task runs and stages "
        "the last one back when a machine dies"
    ),
}

# num_shards is bounded at 999 downstream (flashruntime's federated
# averaging service); a sweep anywhere near that size is far more likely
# a typo (a stray extra axis, a pasted range) than an intentional launch,
# so the cap is set an order of magnitude below that for headroom.
MAX_SWEEP_COMBINATIONS = 100

# One day. Long enough for a real training run on a volunteer node,
# short enough that a runaway timeout can't hold a lease hostage
# indefinitely.
MAX_TIMEOUT_SECONDS = 24 * 60 * 60

#: Decimal ZC per machine-hour to integer millicredits conversion factor.
#: Evidence: migrations/0018_marketplace.sql:366 (ask_zc_per_hour column comment:
#: "Millicredits per machine-hour"); apps/web/components/market/ListingsPanel.tsx:792
#: (parseZcToMzc multiplies by 1000).
ZC_PER_HOUR_UNIT = 1000

REQUIRED_KEYS = {"version", "name", "image", "entrypoint"}
OPTIONAL_KEYS = {"args", "sweep", "resources", "timeout_seconds",
                 "mode", "epochs", "sync_every",
                 "local_inputs", "partition", "validators", "reduce",
                 "allow_partial", "dependencies", "datasets", "python", "price"}
ALLOWED_KEYS = REQUIRED_KEYS | OPTIONAL_KEYS

#: The shape a ``python:`` declaration may take: CPython ``major.minor``.
#:
#: Major.minor and nothing longer, because that is the granularity a wheel
#: tag has — ``cp312`` — and the granularity ``uv python install`` resolves.
#: A patch level (``3.11.9``) would promise a precision no host can honour
#: and no wheel distinguishes; an implementation prefix (``pypy3.11``) names
#: an interpreter the curated manifests were never resolved against.
#:
#: ``3.`` and not ``\d+\.`` deliberately: there is no Python 4, and a job
#: that asks for one is a typo the fleet cannot satisfy, not a forward
#: declaration worth accepting quietly.
#:
#: Imported by ``flashml_cloud_api.compile`` rather than restated there, so
#: the file's own check and the compiler's independent re-check (see
#: ``compile._declared_python``) can never drift into two answers.
PYTHON_VERSION_RE = re.compile(r"^3\.\d+$")

#: Today's behaviour, and the default: one round of independent tasks (a
#: sweep, or a single task). Nothing about an ``independent`` config changes
#: because ``federated`` now exists.
MODE_INDEPENDENT = "independent"

#: Federated averaging: the same entrypoint runs once per shard per round,
#: and the API drives the rounds. This is a **contract the user opts into**
#: — see ``preflight``'s ``federated-contract`` check — not something that
#: can be inferred from arbitrary repo code.
MODE_FEDERATED = "federated"

MODES = (MODE_INDEPENDENT, MODE_FEDERATED)

#: A federated run submits one coordinator job per round and the driver
#: holds the loop in memory for its whole duration. The cap is not about
#: arithmetic — it is that each round is a full submit/lease/commit cycle
#: across volunteer machines, so a four-digit round count is a runaway, not
#: a plan.
MAX_ROUNDS = 500

#: The alphabet a ``local_inputs`` label may use.
#:
#: **This is a deliberate duplicate** of flashnode's
#: ``flashnode/config/local_data.py::LABEL_RE``. It cannot be imported: the
#: agent lives in the public ``Zolli-Labs/flashml`` repo and this repo never
#: imports it (AGENTS.md hard rule 2 — the only thing that crosses that
#: boundary is ``flashruntime.protocol``, and a label alphabet is not a wire
#: schema). So the rule is restated here, on purpose, with the two copies
#: pinned to each other by this comment.
#:
#: Both ends must check, because they refuse at different moments. flashnode
#: is the last line of defence — it is the process that holds the directory
#: and joins nothing to a host path. But by the time it refuses, the job has
#: been submitted, compiled, leased and claimed, and its author sees an
#: opaque node-side failure. Refusing here is the earliest point at which a
#: job author can be told they typed the label wrong.
#:
#: Narrower than any filesystem: ``/``, ``..`` and whitespace are exactly the
#: characters that turn a name into a traversal, and they are not expressible.
LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

#: The four addressing schemes v1 understands. All four are fetched
#: ANONYMOUSLY: the scheme says where the bytes are, not who may read them,
#: and v1 stores no credential of any kind.
#: The most datasets one job may declare.
#:
#: Not an arbitrary tidiness limit. Each declared source is resolved
#: SERIALLY against its origin inside the submit request, under a 30s
#: per-request timeout, so N sources is an N x 30s worst case on a live
#: HTTP handler. Four keeps that bounded at two minutes without needing
#: concurrency, and nobody legitimately trains one job on ten origins.
MAX_DATASETS = 4

DATASET_SCHEMES = ("hf://", "s3://", "r2://", "https://")

#: The two ways one declared dataset becomes N per-task slices. ``shard``
#: cuts it into disjoint pieces whose union is one pass; ``replica`` hands
#: every task the whole thing.
SPLIT_SHARD = "shard"
SPLIT_REPLICA = "replica"
SPLITS = (SPLIT_SHARD, SPLIT_REPLICA)

#: What one dataset entry may say. Restated as a constant because the
#: message that refuses an unknown key has to list them, and a list that
#: drifts from the check is worse than no list at all.
DATASET_KEYS = ("name", "source", "select", "split")


class ConfigError(Exception):
    """Raised for any flashml.yaml that fails validation, including
    malformed YAML itself — callers never need to catch a raw
    ``yaml.YAMLError`` separately."""


@dataclass(frozen=True)
class FlashmlConfig:
    version: int
    name: str
    image: str
    entrypoint: str
    args: list[str] = field(default_factory=list)
    sweep: dict[str, list] = field(default_factory=dict)
    resources: dict = field(default_factory=dict)
    timeout_seconds: int | None = None
    #: Labels naming data the *host* lends to the task, mounted read-only by
    #: the agent. Never uploaded, never an artifact, never a path: a job names
    #: a label and the host owner alone decides what directory it means.
    #: Empty is the default and the overwhelmingly common case.
    local_inputs: list[str] = field(default_factory=list)
    #: ``independent`` (default, unchanged) or ``federated``.
    mode: str = MODE_INDEPENDENT
    #: Federated only. ``None`` under ``independent``, so a caller that
    #: reads these without checking the mode gets a TypeError rather than a
    #: plausible-looking default.
    #:
    #: ``epochs`` is total passes over the data and ``sync_every`` is passes
    #: between combines — the two training decisions, both independent of
    #: who happens to be online. Neither says anything about the fleet: how
    #: finely a pass is cut and how many machines may claim are derived at
    #: submit time from the Crew (see ``fedavg.py``).
    epochs: int | None = None
    sync_every: float | None = None
    #: The `partition` generator — one task per shard of an enumerated range
    #: (§4). Mutually exclusive with ``sweep``: both are generators, and a
    #: job has exactly one.
    partition: dict = field(default_factory=dict)
    #: Declared output validators (§5), e.g. ``{"keys": ["accuracy"]}``.
    validators: dict = field(default_factory=dict)
    #: The job-level reducer (§6), e.g. ``{"kind": "rank", "metric": "acc"}``.
    reduce: dict = field(default_factory=dict)
    #: Accept a run whose tasks did not all succeed (§8). Fail-closed, like
    #: the ``retryPolicy.allowPartial`` field it sets: without it one task
    #: exhausting its attempts fails the whole job, which on a fleet of
    #: volunteer machines discards every other machine's accepted work.
    allow_partial: bool = False
    #: Extra pip requirement lines the job needs beyond its image's own
    #: manifest — e.g. ``["transformers==4.44.0"]``. The compiler (not this
    #: module) resolves the image to its base manifest and appends these; see
    #: ``flashml_cloud_api.compile._dependencies``. Empty is the default and
    #: the overwhelmingly common case: a curated image is a head start, not a
    #: whitelist a job must restate to use.
    dependencies: list[str] = field(default_factory=list)
    #: Public origins the *host* fetches before the task starts — never this
    #: API, which reads a file listing and hands out URLs. Each entry is
    #: ``{"name", "source", "select", "split"}`` with ``select``/``split``
    #: ``None`` when unwritten; ``split`` is inferred from ``mode`` by the
    #: compiler, not here (see ``_validate_datasets``). Empty is the default
    #: and the overwhelmingly common case.
    datasets: list[dict] = field(default_factory=list)
    #: The CPython ``major.minor`` this job's dependencies were pinned
    #: against — ``"3.11"`` — or ``None``, which is the default and the
    #: overwhelmingly common case.
    #:
    #: The same class of key as ``dependencies`` above, and the same
    #: doctrine: the image manifest is the base and the yaml refines it. A
    #: curated image already implies an interpreter (the compiler's
    #: ``CURATED_IMAGE_PYTHON`` table), so writing this is only necessary for
    #: a custom image — or to overrule the default, which a submitter is
    #: always allowed to do. The compiler resolves the precedence; see
    #: ``flashml_cloud_api.compile.PYTHON_PARAM``.
    #:
    #: It is ADVISORY, not a placement filter. A container host runs the
    #: image's own interpreter and ignores this entirely; it exists for the
    #: hosts that build a venv instead of running a container, which is
    #: where an interpreter mismatch turns a pinned wheel into a source
    #: build (see PYTHON_PARAM for the live evidence).
    python: str | None = None
    #: Market opt-in pricing. ``None`` (default) means today's behavior:
    #: workspace only, free. A price block declares max_per_hour (decimal ZC,
    #: converted to millicredits), optional objective (cheapest/balanced/fastest,
    #: default balanced), and optional budget (millicredits, must cover at least
    #: one hour at the cap).
    price: dict | None = None

    @property
    def is_federated(self) -> bool:
        return self.mode == MODE_FEDERATED

    @property
    def round_count(self) -> int | None:
        """How many rounds this config's training becomes, or ``None`` under
        ``independent``.

        Derived, never typed — see ``derived_round_count``.
        """
        if self.epochs is None or self.sync_every is None:
            return None
        return derived_round_count(self.epochs, self.sync_every)


def derived_round_count(epochs: int, sync_every: float) -> int:
    """Rounds for ``epochs`` passes combining every ``sync_every`` passes.

    **This duplicates ``run_fedavg``'s own derivation**
    (``flashml_workloads/fedavg_driver.py``: ``rounds = max(1,
    ceil(epochs / sync_every))``), which the runtime computes inline and does
    not export. Two copies of one formula across a repo boundary is exactly
    the drift the 2026-08-01 consolidation exists to prevent, so the copy is
    pinned by a test that drives the real driver and asserts it ran this many
    rounds (``tests/test_elastic_driver.py``). Delete this function in favour
    of the runtime's own the release it starts exporting one.

    Why the API needs it at all: the driver is restartable, and its resume
    check has to know whether every round is already recorded before it
    decides a re-triggered job is finished rather than looping backwards.
    """
    return max(1, ceil(epochs / sync_every))


def _require_string(raw: dict, key: str) -> str:
    if key not in raw:
        raise ConfigError(f"flashml.yaml is missing required key {key!r}")
    value = raw[key]
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"flashml.yaml {key!r} must be a non-empty string, got {value!r}")
    return value


def parse_flashml_yaml(text: str) -> FlashmlConfig:
    """Parse and fully validate a flashml.yaml's text. Raises
    ``ConfigError`` — never a bare ``yaml.YAMLError``, ``KeyError``, or
    ``TypeError`` — for anything that doesn't produce a safe, complete
    config."""
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"flashml.yaml is not valid YAML: {exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigError("flashml.yaml must be a mapping at the top level")

    refused = sorted(set(raw) & set(REFUSED_KEYS))
    if refused:
        raise ConfigError(
            "flashml.yaml sets " + ", ".join(
                f"{key!r} ({REFUSED_KEYS[key]})" for key in refused
            ) + "."
        )

    unknown = set(raw) - ALLOWED_KEYS
    if unknown:
        raise ConfigError(
            f"flashml.yaml has unknown key(s) {sorted(unknown)!r}; "
            f"allowed keys are {sorted(ALLOWED_KEYS)!r}"
        )

    if "version" not in raw:
        raise ConfigError("flashml.yaml is missing required key 'version'")
    version = raw["version"]
    if version not in SUPPORTED_VERSIONS:
        raise ConfigError(
            f"flashml.yaml 'version' must be one of {list(SUPPORTED_VERSIONS)!r}, "
            f"got {version!r}"
        )

    name = _require_string(raw, "name")
    image = _require_string(raw, "image")
    entrypoint = _require_string(raw, "entrypoint")

    args = _validate_args(raw.get("args", []))
    sweep = _validate_sweep(raw.get("sweep", {}))
    resources = _validate_resources(raw.get("resources", {}))
    timeout_seconds = _validate_timeout_seconds(raw.get("timeout_seconds"))
    local_inputs = _validate_local_inputs(raw.get("local_inputs", []))
    partition = _validate_partition(raw)
    validators = _validate_mapping(raw.get("validators"), "validators")
    reduce_spec = _validate_reduce(raw.get("reduce"))
    allow_partial = _validate_bool(raw.get("allow_partial"), "allow_partial")
    dependencies = _validate_dependencies(raw.get("dependencies", []))
    datasets = _validate_datasets(raw.get("datasets"))
    python = _validate_python(raw.get("python"))
    price = _validate_price(raw.get("price")) if raw.get("price") is not None else None
    mode, epochs, sync_every = _validate_mode(raw, version)

    return FlashmlConfig(
        version=version,
        name=name,
        image=image,
        entrypoint=entrypoint,
        args=args,
        sweep=sweep,
        resources=resources,
        timeout_seconds=timeout_seconds,
        local_inputs=local_inputs,
        mode=mode,
        epochs=epochs,
        sync_every=sync_every,
        partition=partition,
        validators=validators,
        reduce=reduce_spec,
        allow_partial=allow_partial,
        dependencies=dependencies,
        datasets=datasets,
        python=python,
        price=price,
    )


def _positive_int(raw: dict, key: str, maximum: int) -> int:
    value = raw[key]
    # bool before int: `True` is an int in Python, and `rounds: true` is a
    # typo that would otherwise silently mean one round.
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"flashml.yaml {key!r} must be an integer, got {value!r}")
    if value < 1:
        raise ConfigError(f"flashml.yaml {key!r} must be >= 1, got {value!r}")
    if value > maximum:
        raise ConfigError(
            f"flashml.yaml {key!r} is {value}, above the cap of {maximum}"
        )
    return value


#: The default: one combine per pass over the data. Chosen because it
#: reproduces the behaviour every shipped federated job already has —
#: ``rounds == epochs`` — so adding the knob changes nothing for anyone who
#: does not set it.
DEFAULT_SYNC_EVERY = 1.0

#: The training fields, which apply only to ``mode: federated``.
TRAINING_KEYS = ("epochs", "sync_every")


def _validate_mode(raw: dict, version: int) -> tuple[str, int | None, float | None]:
    """``(mode, epochs, sync_every)``.

    Federated-only keys are refused under ``independent`` rather than
    ignored, for the same reason unknown top-level keys are: a config that
    names ``epochs`` has an author who believes several passes are happening,
    and silently running one instead is the kind of "worked, but not the
    thing you asked for" outcome this parser exists to prevent.
    """
    mode = raw.get("mode", MODE_INDEPENDENT)
    if mode not in MODES:
        raise ConfigError(
            f"flashml.yaml 'mode' must be one of {list(MODES)!r}, got {mode!r}"
        )

    if mode == MODE_INDEPENDENT:
        present = [k for k in TRAINING_KEYS if k in raw]
        if present:
            raise ConfigError(
                f"flashml.yaml sets {sorted(present)!r}, which only apply to "
                f"'mode: {MODE_FEDERATED}'; the default mode "
                f"('{MODE_INDEPENDENT}') runs one round of independent tasks"
            )
        return MODE_INDEPENDENT, None, None

    if version < FEDERATED_VERSION:
        raise ConfigError(
            f"flashml.yaml 'mode: {MODE_FEDERATED}' requires "
            f"'version: {FEDERATED_VERSION}'. Version {version} federated "
            f"configs set 'rounds', 'min_participants' and 'shards'; version "
            f"{FEDERATED_VERSION} replaces all three with 'epochs' (total "
            f"passes over your data) and the optional 'sync_every' (passes "
            f"between combines, default {DEFAULT_SYNC_EVERY}). How finely the "
            f"work is cut and how many machines take part are no longer "
            f"yours to state — they are derived from whoever is online"
        )

    if "epochs" not in raw:
        raise ConfigError(
            f"flashml.yaml 'mode: {MODE_FEDERATED}' also requires 'epochs' — "
            f"how many passes over your data to train for. There is no "
            f"sensible default for how much training someone wants"
        )

    epochs = _positive_int(raw, "epochs", MAX_ROUNDS)
    sync_every = _validate_sync_every(raw.get("sync_every", DEFAULT_SYNC_EVERY))

    rounds = derived_round_count(epochs, sync_every)
    if rounds > MAX_ROUNDS:
        raise ConfigError(
            f"flashml.yaml 'epochs' ({epochs}) combining every "
            f"'sync_every' ({sync_every}) pass(es) is {rounds} rounds, above "
            f"the cap of {MAX_ROUNDS}. Each round is a full "
            f"submit/lease/commit cycle across volunteer machines, so a "
            f"four-digit round count is a runaway rather than a plan — raise "
            f"'sync_every' to combine less often, or lower 'epochs'"
        )

    if raw.get("partition"):
        # Same collision as `sweep` below, for the same reason: a federated
        # round already cuts one pass over the data into chunks, and a
        # partition would be a second, conflicting split of the same data.
        raise ConfigError(
            f"flashml.yaml cannot combine 'partition' with 'mode: {MODE_FEDERATED}': "
            f"a federated round already cuts one pass over your data into "
            f"chunks, and a partition would be a second, conflicting split"
        )

    if raw.get("sweep"):
        # A sweep expands to one task per hyperparameter combination; a
        # federated round expands to one task per shard. Both cannot be the
        # meaning of the same task list, and quietly picking one would train
        # something the author did not describe.
        raise ConfigError(
            f"flashml.yaml cannot combine 'sweep' with 'mode: {MODE_FEDERATED}': "
            f"a sweep's tasks are independent trials, a federated round's tasks "
            f"are chunks of one model's data"
        )

    return MODE_FEDERATED, epochs, sync_every


def _validate_sync_every(value: object) -> float:
    """Passes of data between combines: ``0 < sync_every <= 1.0``.

    The upper bound is the runtime's own (``run_fedavg`` refuses the same
    range): a round's coverage target is ``sync_every`` of one pass, and a
    round cannot cover more of a pass than the pass contains, so a value
    above 1.0 would describe a round that can never close.

    An integer ``1`` is accepted as ``1.0``. YAML gives an author no way to
    know which of the two this parser wanted, and refusing the one they typed
    would be a rule about notation rather than about their training.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(
            f"flashml.yaml 'sync_every' must be a number of passes, got {value!r}"
        )
    number = float(value)
    if not 0 < number <= 1.0:
        raise ConfigError(
            f"flashml.yaml 'sync_every' must be greater than 0 and at most "
            f"1.0, got {number!r}: it is how much of one pass over your data a "
            f"round covers before the machines combine, and a round cannot "
            f"cover more of a pass than the pass contains"
        )
    if number != DEFAULT_SYNC_EVERY:
        # Refused rather than honoured, and this is a capability limit rather
        # than a rule about the value. A round below a full pass only works
        # if a machine can train SEVERAL chunks and report every id it
        # finished, because a pass then has more chunks than the round has
        # slots. The built-in worker does that; a repo's own entrypoint is
        # handed one chunk and reports none, so cutting the pass finer than
        # the fleet would leave part of the data never trained (the rotation
        # would stride straight past it), and cutting it no finer would close
        # the round at `sync_every` of what it dispatched — throwing away the
        # work of every machine still training.
        #
        # Both failures are silent and both spend volunteers' electricity for
        # nothing, so the field is accepted by the schema, validated, and then
        # refused with the reason until the worker contract catches up.
        raise ConfigError(
            f"flashml.yaml 'sync_every' is {number}, and only "
            f"{DEFAULT_SYNC_EVERY} works today: combining more often than "
            f"once per pass needs your entrypoint to train a sequence of data "
            f"chunks and report which ones it finished ('chunks_done' in "
            f"metrics.json). Until it does, each machine is handed one chunk "
            f"per round, so a round is one full pass over your data. Set "
            f"'epochs' for how much training you want"
        )
    return number


def _validate_bool(value: object, key: str) -> bool:
    # Not `bool(value)`: YAML turns an unquoted `yes-please` into the STRING
    # "yes-please", which is truthy, so coercing would silently turn a typo
    # into an opt-in to changed failure semantics.
    if value is None:
        return False
    if not isinstance(value, bool):
        raise ConfigError(
            f"flashml.yaml {key!r} must be true or false, got {value!r}"
        )
    return value


def _validate_mapping(value: object, key: str) -> dict:
    """A key that carries a nested object, or nothing.

    Validation here is STRUCTURAL only. The authoritative semantic rules —
    which reducers exist, which need a metric, how many shards a range may
    produce — live in flashruntime and are enforced at expansion. Restating
    them here would create two copies that drift, and the copy the user
    hits first would be the one that is wrong.
    """
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigError(
            f"flashml.yaml {key!r} must be a mapping, got {value!r}"
        )
    return dict(value)


def _validate_partition(raw: dict) -> dict:
    partition = _validate_mapping(raw.get("partition"), "partition")
    if partition and raw.get("sweep"):
        raise ConfigError(
            "flashml.yaml cannot combine 'sweep' with 'partition': both are "
            "generators, and a job has exactly one — a sweep fans out over "
            "hyperparameter combinations, a partition over shards of a range"
        )
    if partition and raw.get("datasets"):
        # Refused here rather than left to fail later, and the "later" is the
        # problem: a partition job carries no `task_params` (the coordinator
        # expands the range itself), so the compiler cuts ONE slice list while
        # the recipe expands N tasks. The mismatch surfaces as a ValueError
        # inside `CommandRecipe.expand` — after the route has answered 201, in
        # a code path the submitter never sees.
        #
        # It is also a genuine ambiguity, not merely a plumbing gap: partition
        # already shards a numeric range, `datasets` already shards a file
        # list, and nothing says which of the two a given task's slice should
        # follow. Deciding that silently would be worse than refusing.
        raise ConfigError(
            "flashml.yaml cannot combine 'partition' with 'datasets': both "
            "decide how one job is cut into tasks, and a job has exactly one "
            "such decision — 'partition' splits a numeric range, 'datasets' "
            "splits a file list. Use a sweep with 'datasets', or drop "
            "'datasets' and address your data from the partition indices"
        )
    return partition


def _validate_reduce(value: object) -> dict:
    reduce_spec = _validate_mapping(value, "reduce")
    if reduce_spec and not reduce_spec.get("kind"):
        # The one semantic rule restated here on purpose: without `kind`
        # there is nothing for the coordinator to look up, and
        # `reduce: {metric: acc}` is a plausible typo that would otherwise
        # mean no reduction at all — silently, after the job ran.
        raise ConfigError(
            "flashml.yaml 'reduce' needs a 'kind' naming the reducer "
            "(none, collect, rank, aggregate, concat)"
        )
    return reduce_spec


def _validate_args(args: object) -> list[str]:
    # An argv list, not a shell string: there is no shell on the other
    # end (the executor runs `python <entrypoint> *args` directly), so a
    # string here would either be silently wrong (passed as one giant
    # argument) or invite an entirely unnecessary shell-injection worry.
    if not isinstance(args, list) or isinstance(args, (str, bytes)):
        raise ConfigError(
            f"flashml.yaml 'args' must be a list of strings (an argv list, "
            f"not a shell string), got {args!r}"
        )
    if not all(isinstance(a, str) for a in args):
        raise ConfigError(f"flashml.yaml 'args' must be a list of strings, got {args!r}")
    return list(args)


def _validate_dependencies(value: object) -> list[str]:
    # A list of pip requirement lines, not a single string — the same shape
    # judgement as `args` above, for the same reason: `dependencies:
    # torch==2.3.1` is a plausible typo that would otherwise silently parse
    # as a list of characters rather than fail loudly.
    if not isinstance(value, list) or isinstance(value, (str, bytes)):
        raise ConfigError(
            f"flashml.yaml 'dependencies' must be a list of pip requirement "
            f"strings (not a single string), got {value!r}"
        )
    if not all(isinstance(d, str) for d in value):
        raise ConfigError(
            f"flashml.yaml 'dependencies' must be a list of strings, got {value!r}"
        )
    return list(value)


def _validate_python(value: object) -> str | None:
    """``python: "3.11"`` → ``"3.11"``; absent → ``None``.

    **Quoting is load-bearing and the message says so**, because the failure
    is silent otherwise: YAML reads an unquoted ``python: 3.11`` as a FLOAT,
    and ``python: 3.10`` as the float ``3.1`` — a job asking for 3.10 would
    otherwise be handed an interpreter that has been end-of-life since 2020.
    So a non-string is refused here rather than coerced, and the refusal
    shows the quoted form.

    One shape, ``PYTHON_VERSION_RE``, checked here and re-checked
    independently by the compiler — see ``compile._declared_python`` for why
    a second check on the same string is not ceremony.
    """
    if value is None:
        return None
    if not isinstance(value, str) or not PYTHON_VERSION_RE.match(value):
        raise ConfigError(
            f"flashml.yaml 'python' must be a CPython major.minor version "
            f'written as a QUOTED string — e.g. python: "3.11" — got '
            f"{value!r}. Not a patch level (\"3.11.9\"), not another "
            f'implementation ("pypy3.11"), and not unquoted: YAML reads a '
            f"bare 3.10 as the number 3.1"
        )
    return value


def _validate_local_inputs(value: object) -> list[str]:
    """``local_inputs: ["patients"]`` → ``["patients"]``; absent → ``[]``.

    A list of **labels**, never a mapping and never a path. The distinction
    is the security property of the whole feature: a job says *what* it needs
    by name, and the host owner alone decides which directory that name means
    (``FLASHNODE_LOCAL_DATA`` on their machine). A mapping here would let a
    submitter name a host path, so the type check below refuses one for the
    same reason ``args`` refuses a shell string — the shape carries meaning.

    Labels are validated against ``LABEL_RE``, flashnode's alphabet restated
    (see its comment). Refusing at submit time is the point: the author is
    here now, and a label rejected on a volunteer's machine ten minutes later
    reads as a platform fault rather than a typo.
    """
    if not isinstance(value, list) or isinstance(value, (str, bytes)):
        raise ConfigError(
            f"flashml.yaml 'local_inputs' must be a list of dataset labels "
            f"(names the host advertises, never paths), got {value!r}"
        )
    for label in value:
        if not isinstance(label, str):
            raise ConfigError(
                f"flashml.yaml 'local_inputs' must contain only strings, got "
                f"{label!r} in {value!r}"
            )
        # `.` and `..` are named explicitly as well as being unmatched by
        # LABEL_RE: flashnode does the same, and a belt-and-braces check on
        # the two strings that mean "a directory other than this one" is
        # worth keeping identical at both ends.
        if not LABEL_RE.match(label) or label in (".", ".."):
            raise ConfigError(
                f"flashml.yaml 'local_inputs' contains an illegal dataset "
                f"label {label!r}: a label is a name, not a path — it must "
                f"start with a letter or digit and use only [A-Za-z0-9._-]"
            )
    return list(value)


def _validate_datasets(value: object) -> list[dict]:
    """``datasets:`` — public origins the HOST fetches, never this API.

    The counterpart to ``local_inputs``: that key names data a host already
    holds and will not send anywhere, this one names data a host will go and
    get. Neither ever moves through the control plane, which is what keeps a
    job's marginal cost at zero.

    ``split`` is deliberately NOT defaulted here. It is inferred from
    ``mode`` in the compiler (federated → shard, everything else →
    replica), and a default written in at parse time would silently
    outrank that inference. ``None`` means "the user did not say".
    """
    if value is None:
        return []
    if not isinstance(value, list) or isinstance(value, (str, bytes)):
        raise ConfigError(
            f"flashml.yaml 'datasets' must be a list of mappings, each with a "
            f"'name' and a 'source', got {value!r}"
        )
    if len(value) > MAX_DATASETS:
        raise ConfigError(
            f"flashml.yaml 'datasets' declares {len(value)}, and at most "
            f"{MAX_DATASETS} are allowed: each one is resolved against its "
            f"origin while your submit request waits"
        )
    out: list[dict] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            raise ConfigError(
                f"flashml.yaml 'datasets' entries must be mappings, got {item!r}"
            )
        unknown = set(item) - set(DATASET_KEYS)
        if unknown:
            raise ConfigError(
                f"flashml.yaml dataset has unknown key(s) {sorted(unknown)!r}; "
                f"allowed keys are {sorted(DATASET_KEYS)!r}"
            )
        name = item.get("name")
        # The same alphabet `local_inputs` labels use, for the same reason:
        # the name becomes a directory on a volunteer's machine, and '/',
        # '..' and whitespace are exactly what turns a name into a traversal.
        if not isinstance(name, str) or not LABEL_RE.match(name) or name in (".", ".."):
            raise ConfigError(
                f"flashml.yaml dataset 'name' must be a name, not a path — it "
                f"becomes a directory on a volunteer's machine, so it must "
                f"start with a letter or digit and use only [A-Za-z0-9._-]; "
                f"got {name!r}"
            )
        if name in seen:
            raise ConfigError(
                f"flashml.yaml dataset 'name' must be unique, got {name!r} "
                f"twice; both would mount at /work/data/{name}/ and overwrite "
                f"each other"
            )
        seen.add(name)
        source = item.get("source")
        if not isinstance(source, str) or not source.strip():
            raise ConfigError(
                f"flashml.yaml dataset {name!r} must have a 'source', got "
                f"{source!r}"
            )
        if not source.startswith(DATASET_SCHEMES):
            raise ConfigError(
                f"flashml.yaml dataset {name!r} has an unsupported source "
                f"{source!r}; supported schemes are {list(DATASET_SCHEMES)!r}"
            )
        select = item.get("select")
        if select is not None and not isinstance(select, str):
            raise ConfigError(
                f"flashml.yaml dataset {name!r} 'select' must be a glob "
                f"string, got {select!r}"
            )
        split = item.get("split")
        if split is not None and split not in SPLITS:
            raise ConfigError(
                f"flashml.yaml dataset {name!r} 'split' must be one of "
                f"{list(SPLITS)!r}, got {split!r}"
            )
        out.append({"name": name, "source": source, "select": select, "split": split})
    return out


def _validate_sweep(sweep: object) -> dict[str, list]:
    if not isinstance(sweep, dict):
        raise ConfigError(f"flashml.yaml 'sweep' must be a mapping, got {sweep!r}")

    validated: dict[str, list] = {}
    for key, values in sweep.items():
        if not isinstance(values, list) or len(values) == 0:
            raise ConfigError(
                f"flashml.yaml 'sweep.{key}' must be a non-empty list, got {values!r}"
            )
        validated[key] = list(values)

    if validated:
        combinations = prod(len(v) for v in validated.values())
        if combinations > MAX_SWEEP_COMBINATIONS:
            raise ConfigError(
                f"flashml.yaml 'sweep' would expand to {combinations} combinations "
                f"(cap is {MAX_SWEEP_COMBINATIONS}); a sweep this large is more "
                f"likely a mistake than an intention"
            )

    return validated


def _validate_resources(resources: object) -> dict:
    if not isinstance(resources, dict):
        raise ConfigError(f"flashml.yaml 'resources' must be a mapping, got {resources!r}")
    return dict(resources)


def _validate_timeout_seconds(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"flashml.yaml 'timeout_seconds' must be an integer, got {value!r}")
    if value <= 0:
        raise ConfigError(f"flashml.yaml 'timeout_seconds' must be positive, got {value!r}")
    if value > MAX_TIMEOUT_SECONDS:
        raise ConfigError(
            f"flashml.yaml 'timeout_seconds' {value} exceeds the cap of "
            f"{MAX_TIMEOUT_SECONDS} ({MAX_TIMEOUT_SECONDS // 3600}h)"
        )
    return value


_OBJECTIVES = ("cheapest", "balanced", "fastest")


def _validate_price(value: object) -> dict:
    """The market opt-in. Absent means today's behavior: workspace only, free.

    ``max_per_hour`` is decimal ZC per machine-hour and converts exactly into
    the ledger's integer unit; a value the unit cannot represent exactly
    (more decimal places than the unit carries) is refused rather than
    rounded — a price the user typed must be the price the bid carries.
    """
    raw = _validate_mapping(value, "price")
    allowed = {"max_per_hour", "objective", "budget"}
    unknown = set(raw) - allowed
    if unknown:
        raise ConfigError(
            f"price: unknown key(s) {sorted(unknown)!r}; "
            f"allowed: {sorted(allowed)!r}"
        )

    def to_unit(field: str, val: object, *, minimum_units: int) -> int:
        try:
            units = Fraction(str(val)) * ZC_PER_HOUR_UNIT
        except (ValueError, ZeroDivisionError):
            raise ConfigError(f"price.{field}: not a number: {val!r}")
        if units.denominator != 1:
            raise ConfigError(
                f"price.{field}: {val!r} has more precision than "
                f"1/{ZC_PER_HOUR_UNIT} ZC"
            )
        if units < minimum_units:
            raise ConfigError(f"price.{field}: must be at least "
                                   f"{minimum_units}/{ZC_PER_HOUR_UNIT} ZC")
        return int(units)

    if "max_per_hour" not in raw:
        raise ConfigError("price: max_per_hour is required")
    max_zc = to_unit("max_per_hour", raw["max_per_hour"], minimum_units=1)

    objective = raw.get("objective", "balanced")
    if objective not in _OBJECTIVES:
        raise ConfigError(
            f"price.objective: {objective!r} is not one of {_OBJECTIVES}"
        )

    budget = None
    if raw.get("budget") is not None:
        budget = to_unit("budget", raw["budget"], minimum_units=max_zc)

    return {"max_zc_per_hour": max_zc, "objective": objective,
            "budget_zc": budget}
